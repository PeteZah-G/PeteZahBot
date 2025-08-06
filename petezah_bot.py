import discord
from discord.ext import commands
import os
import aiohttp
import json
import re
import asyncio
from dotenv import load_dotenv
import urllib.parse
from collections import deque
import datetime
import io
import logging

logging.basicConfig(filename='bot.log', level=logging.INFO, 
                   format='%(asctime)s:%(levelname)s:%(message)s')

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.moderation = True
intents.guilds = True
bot = commands.Bot(command_prefix='p!', intents=intents)

active_channels = set()
disabled_channels = set()
blocked_mentions = [r'@everyone', r'@here']
message_history = {}
warnings = {}
afk_users = {}
pinned_messages = {}
locked_channels = set()
welcome_channels = {}
security_channels = set()
security_servers = set()
SUPERUSER_ID = 1311722282317779097
MOD_ROLE_NAME = "Moderator"

async def generate_ai_response(message):
    logging.info(f"Generating AI response for message: {message.content}")
    channel_id = message.channel.id
    if channel_id not in message_history:
        message_history[channel_id] = deque(maxlen=7)
    message_history[channel_id].append({"role": "user", "content": message.content})
    prompt = "\n".join([f"{msg['role']}: {msg['content']}" for msg in message_history[channel_id]])
    encoded_prompt = urllib.parse.quote(prompt)
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f'https://text.pollinations.ai/{encoded_prompt}',
                timeout=10
            ) as response:
                logging.info(f"Pollinations API response status: {response.status}")
                if response.status == 200:
                    response_text = await response.text()
                    logging.info(f"Pollinations API response: {response_text}")
                    for pattern in blocked_mentions:
                        response_text = re.sub(pattern, '[REDACTED]', response_text, flags=re.IGNORECASE)
                    return response_text[:2000] if len(response_text) > 2000 else response_text
                return f"API error: Status {response.status}"
        except Exception as e:
            logging.error(f"AI response error: {str(e)}")
            return "Error connecting to AI service."

async def generate_image(prompt):
    encoded_prompt = urllib.parse.quote(prompt)
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f'https://image.pollinations.ai/prompt/{encoded_prompt}',
                timeout=10
            ) as response:
                logging.info(f"Pollinations Image API response status: {response.status}")
                if response.status == 200:
                    image_data = await response.read()
                    return io.BytesIO(image_data)
                return None
        except Exception as e:
            logging.error(f"Image generation error: {str(e)}")
            return None

async def notify_user(member, action, reason=None, duration=None):
    try:
        embed = discord.Embed(title=f"You have been {action}", color=discord.Color.red())
        embed.add_field(name="Server", value=member.guild.name, inline=False)
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
        if duration:
            embed.add_field(name="Duration", value=duration, inline=False)
        embed.set_footer(text=f"Action taken at {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        await member.send(embed=embed)
        return True
    except Exception as e:
        logging.error(f"Failed to notify user {member.id}: {str(e)}")
        return False

def parse_duration(duration_str):
    if not duration_str:
        return None, None
    duration_str = duration_str.lower().strip()
    match = re.match(r'^(\d+)(s|m|h|d)?$', duration_str)
    if not match:
        return None, "Invalid duration format. Use <number><unit> (e.g., 5d, 10m, 2h, 30s)."
    amount, unit = match.groups()
    amount = int(amount)
    if unit is None:
        unit = 'm'
    units = {'s': ('seconds', amount), 'm': ('minutes', amount), 'h': ('hours', amount), 'd': ('days', amount)}
    unit_name, seconds = units[unit]
    seconds = amount * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[unit]
    return seconds, f"{amount} {unit_name}"

def is_superuser_or_admin():
    def predicate(ctx):
        return ctx.author.id == SUPERUSER_ID or ctx.author.guild_permissions.administrator
    return commands.check(predicate)

def is_superuser_admin_or_mod():
    def predicate(ctx):
        mod_role = discord.utils.get(ctx.guild.roles, name=MOD_ROLE_NAME)
        has_mod_perms = (mod_role and mod_role in ctx.author.roles and
                        (ctx.author.guild_permissions.manage_messages or
                         ctx.author.guild_permissions.kick_members or
                         ctx.author.guild_permissions.moderate_members))
        return (ctx.author.id == SUPERUSER_ID or 
                ctx.author.guild_permissions.administrator or 
                has_mod_perms)
    return commands.check(predicate)

@bot.event
async def on_ready():
    logging.info(f'Bot is ready as {bot.user} (ID: {bot.user.id})')
    try:
        synced = await bot.tree.sync()
        logging.info(f"Synced {len(synced)} slash command(s)")
        await bot.change_presence(activity=discord.Game(name="PeteZahBot | p!help"))
    except Exception as e:
        logging.error(f"Failed to sync slash commands or set presence: {str(e)}")

@bot.event
async def on_message(message):
    if message.author.bot:
        logging.info(f"Ignoring message from bot {message.author} in channel {message.channel.id}")
        await bot.process_commands(message)
        return

    if message.channel.id in disabled_channels:
        logging.info(f"Ignoring message in disabled channel {message.channel.id}")
        return

    if (message.channel.id in security_channels or message.guild.id in security_servers) and not message.author.bot:
        invite_pattern = r'(discord\.gg|discord\.com/invite|\.gg)/[a-zA-Z0-9]+'
        if re.search(invite_pattern, message.content, re.IGNORECASE):
            try:
                await message.delete()
                await message.author.timeout(datetime.timedelta(minutes=1), reason="Posted a Discord invite link")
                await notify_user(message.author, "timed out", "Posted a Discord invite link", "1 minute")
                await message.channel.send(f"{message.author.mention} has been timed out for 1 minute for posting a Discord invite link.", delete_after=5)
            except Exception as e:
                logging.error(f"Error handling invite link: {str(e)}")

    if message.channel.id not in active_channels:
        logging.info(f"Ignoring message from {message.author} in channel {message.channel.id} (not active)")
        if message.channel.id in pinned_messages and not message.content.startswith('p!'):
            last_message_id = pinned_messages[message.channel.id].get('last_message_id')
            if last_message_id:
                try:
                    last_message = await message.channel.fetch_message(last_message_id)
                    await last_message.delete()
                except discord.NotFound:
                    logging.warning(f"Pinned message {last_message_id} not found, skipping deletion")
                except Exception as e:
                    logging.error(f"Error deleting pinned message: {str(e)}")
            try:
                new_message = await message.channel.send(pinned_messages[message.channel.id]['content'])
                pinned_messages[message.channel.id]['last_message_id'] = new_message.id
            except Exception as e:
                logging.error(f"Error sending pinned message: {str(e)}")
        await bot.process_commands(message)
        return

    logging.info(f"Processing message: {message.content} in channel {message.channel.id}")
    for pattern in blocked_mentions:
        if re.search(pattern, message.content, re.IGNORECASE):
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention}, please don't use mass mentions!", delete_after=5)
            except Exception as e:
                logging.error(f"Error handling blocked mention: {str(e)}")
            return

    try:
        await asyncio.sleep(1)
        ai_response = await generate_ai_response(message)
        message_history[message.channel.id].append({"role": "assistant", "content": ai_response})
        await message.channel.send(ai_response)
    except Exception as e:
        logging.error(f"Error processing AI response: {str(e)}")

    if message.channel.id in pinned_messages and not message.content.startswith('p!'):
        last_message_id = pinned_messages[message.channel.id].get('last_message_id')
        if last_message_id:
            try:
                last_message = await message.channel.fetch_message(last_message_id)
                await last_message.delete()
            except discord.NotFound:
                logging.warning(f"Pinned message {last_message_id} not found, skipping deletion")
            except Exception as e:
                logging.error(f"Error deleting pinned message: {str(e)}")
        try:
            new_message = await message.channel.send(pinned_messages[message.channel.id]['content'])
            pinned_messages[message.channel.id]['last_message_id'] = new_message.id
        except Exception as e:
            logging.error(f"Error sending pinned message: {str(e)}")

    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    try:
        for channel_id, message in welcome_channels.items():
            channel = member.guild.get_channel(channel_id)
            if channel:
                await channel.send(f"Welcome {member.mention} to {member.guild.name}. {message}")
    except Exception as e:
        logging.error(f"Error in on_member_join: {str(e)}")

@bot.command()
@is_superuser_or_admin()
async def initiate(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    logging.info(f"Received p!initiate in channel {ctx.channel.id} by {ctx.author}")
    try:
        if ctx.channel.id not in active_channels:
            active_channels.add(ctx.channel.id)
            await ctx.send("PeteZahBot AI is now active in this channel!")
        else:
            await ctx.send("PeteZahBot AI is already active here!")
    except Exception as e:
        logging.error(f"Error in initiate command: {str(e)}")
        await ctx.send("An error occurred while activating the bot.")

@bot.command()
@is_superuser_or_admin()
async def stop(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if ctx.channel.id in active_channels:
            active_channels.remove(ctx.channel.id)
            if ctx.channel.id in message_history:
                del message_history[ctx.channel.id]
            await ctx.send("PeteZahBot AI is now disabled in this channel!")
        else:
            await ctx.send("PeteZahBot AI is not active in this channel!")
    except Exception as e:
        logging.error(f"Error in stop command: {str(e)}")
        await ctx.send("An error occurred while disabling the bot.")

@bot.command()
@is_superuser_or_admin()
async def ban(ctx, member: discord.Member, duration: str = None, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if member.id == SUPERUSER_ID:
            await ctx.send("This user is immune to bans!")
            return
        if member == ctx.author or member == ctx.guild.me:
            await ctx.send("You can't ban yourself or the bot!")
            return
        duration_seconds, duration_text = parse_duration(duration)
        if duration_seconds is None and duration_text:
            await ctx.send(duration_text)
            return
        notified = await notify_user(member, "banned", reason, duration_text)
        await member.ban(reason=reason)
        await ctx.send(f"{member.mention} has been banned{' and DM\'d' if notified else ''}.{' Duration: ' + duration_text if duration_text else ''} Reason: {reason or 'None'}")
        if duration_seconds:
            await asyncio.sleep(duration_seconds)
            try:
                await ctx.guild.unban(member, reason="Temporary ban duration expired")
                await notify_user(member, "unbanned", "Temporary ban duration expired")
            except discord.NotFound:
                logging.info(f"User {member.id} not found for unban, possibly already unbanned")
            except Exception as e:
                logging.error(f"Error in auto-unban: {str(e)}")
    except Exception as e:
        logging.error(f"Error in ban command: {str(e)}")
        await ctx.send("An error occurred while banning the user.")

@bot.command()
@is_superuser_or_admin()
async def unban(ctx, user_id: int, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=reason)
        await ctx.send(f"{user.name}#{user.discriminator} has been unbanned. Reason: {reason or 'None'}")
    except discord.NotFound:
        await ctx.send("User not found or not banned.")
    except Exception as e:
        logging.error(f"Error in unban command: {str(e)}")
        await ctx.send("An error occurred while unbanning the user.")

@bot.command()
@is_superuser_admin_or_mod()
async def kick(ctx, member: discord.Member, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if member.id == SUPERUSER_ID:
            await ctx.send("This user is immune to kicks!")
            return
        if member == ctx.author or member == ctx.guild.me:
            await ctx.send("You can't kick yourself or the bot!")
            return
        notified = await notify_user(member, "kicked", reason)
        await member.kick(reason=reason)
        await ctx.send(f"{member.mention} has been kicked{' and DM\'d' if notified else ''}. Reason: {reason or 'None'}")
    except Exception as e:
        logging.error(f"Error in kick command: {str(e)}")
        await ctx.send("An error occurred while kicking the user.")

@bot.command()
@is_superuser_admin_or_mod()
async def mute(ctx, member: discord.Member, duration: str = None, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if member.id == SUPERUSER_ID:
            await ctx.send("This user is immune to mutes!")
            return
        if member == ctx.author or member == ctx.guild.me:
            await ctx.send("You can't mute yourself or the bot!")
            return
        mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
        if not mute_role:
            mute_role = await ctx.guild.create_role(name="Muted")
            for channel in ctx.guild.channels:
                await channel.set_permissions(mute_role, send_messages=False)
        duration_seconds, duration_text = parse_duration(duration)
        if duration_seconds is None and duration_text:
            await ctx.send(duration_text)
            return
        notified = await notify_user(member, "muted", reason, duration_text)
        await member.add_roles(mute_role, reason=reason)
        await ctx.send(f"{member.mention} has been muted{' and DM\'d' if notified else ''}.{' Duration: ' + duration_text if duration_text else ''} Reason: {reason or 'None'}")
        if duration_seconds:
            await asyncio.sleep(duration_seconds)
            try:
                if mute_role in member.roles:
                    await member.remove_roles(mute_role, reason="Temporary mute duration expired")
                    await notify_user(member, "unmuted", "Temporary mute duration expired")
            except discord.NotFound:
                logging.info(f"User {member.id} not found for unmute, possibly left server")
            except Exception as e:
                logging.error(f"Error in auto-unmute: {str(e)}")
    except Exception as e:
        logging.error(f"Error in mute command: {str(e)}")
        await ctx.send("An error occurred while muting the user.")

@bot.command()
@is_superuser_or_admin()
async def unmute(ctx, member: discord.Member, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if member == ctx.author or member == ctx.guild.me:
            await ctx.send("You can't unmute yourself or the bot!")
            return
        mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
        if mute_role and mute_role in member.roles:
            notified = await notify_user(member, "unmuted", reason)
            await member.remove_roles(mute_role, reason=reason)
            await ctx.send(f"{member.mention} has been unmuted{' and DM\'d' if notified else ''}. Reason: {reason or 'None'}")
        else:
            await ctx.send(f"{member.mention} is not muted!")
    except Exception as e:
        logging.error(f"Error in unmute command: {str(e)}")
        await ctx.send("An error occurred while unmuting the user.")

@bot.command()
@is_superuser_admin_or_mod()
async def purge(ctx, amount: int):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if amount < 1 or amount > 100:
            await ctx.send("Please specify a number between 1 and 100.")
            return
        await ctx.channel.purge(limit=amount + 1)
        await ctx.send(f"Purged {amount} messages.", delete_after=5)
    except Exception as e:
        logging.error(f"Error in purge command: {str(e)}")
        await ctx.send("An error occurred while purging messages.")

@bot.command()
@is_superuser_or_admin()
async def lock(ctx, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if ctx.channel.id in locked_channels:
            await ctx.send("Channel is already locked!")
            return
        locked_channels.add(ctx.channel.id)
        overwrite_default = ctx.channel.overwrites_for(ctx.guild.default_role)
        overwrite_default.send_messages = False
        overwrite_superuser = ctx.channel.overwrites_for(await bot.fetch_user(SUPERUSER_ID))
        overwrite_superuser.send_messages = True
        await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite_default)
        await ctx.channel.set_permissions(await bot.fetch_user(SUPERUSER_ID), overwrite=overwrite_superuser)
        await ctx.send(f"Channel locked. Only <@{SUPERUSER_ID}> can send messages. Reason: {reason or 'None'}")
    except Exception as e:
        logging.error(f"Error in lock command: {str(e)}")
        await ctx.send("An error occurred while locking the channel.")

@bot.command()
@is_superuser_or_admin()
async def unlock(ctx, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if ctx.channel.id not in locked_channels:
            await ctx.send("Channel is not locked!")
            return
        locked_channels.remove(ctx.channel.id)
        overwrite_default = ctx.channel.overwrites_for(ctx.guild.default_role)
        overwrite_default.send_messages = None
        overwrite_superuser = ctx.channel.overwrites_for(await bot.fetch_user(SUPERUSER_ID))
        overwrite_superuser.send_messages = None
        await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite_default)
        await ctx.channel.set_permissions(await bot.fetch_user(SUPERUSER_ID), overwrite=overwrite_superuser)
        await ctx.send(f"Channel unlocked. Reason: {reason or 'None'}")
    except Exception as e:
        logging.error(f"Error in unlock command: {str(e)}")
        await ctx.send("An error occurred while unlocking the channel.")

@bot.command()
@is_superuser_or_admin()
async def petezah(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if ctx.author.id != SUPERUSER_ID:
            await ctx.send("Only the superuser can use this command!")
            return
        role = discord.utils.get(ctx.guild.roles, name="PeteZah")
        if not role:
            role = await ctx.guild.create_role(
                name="PeteZah",
                permissions=discord.Permissions(administrator=True),
                reason="Created PeteZah role for superuser"
            )
        await ctx.author.add_roles(role)
        await ctx.send(f"PeteZah role created and assigned to <@{SUPERUSER_ID}> with administrator permissions!")
    except Exception as e:
        logging.error(f"Error in petezah command: {str(e)}")
        await ctx.send("An error occurred while creating/assigning the PeteZah role.")

@bot.command()
async def ping(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        latency = round(bot.latency * 1000)
        await ctx.send(f"Pong! Latency: {latency}ms")
    except Exception as e:
        logging.error(f"Error in ping command: {str(e)}")
        await ctx.send("An error occurred while checking latency.")

@bot.command()
async def userinfo(ctx, member: discord.Member = None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        member = member or ctx.author
        embed = discord.Embed(title=f"User Info - {member}", color=discord.Color.blue())
        embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
        embed.add_field(name="ID", value=member.id, inline=True)
        embed.add_field(name="Username", value=member.name, inline=True)
        embed.add_field(name="Discriminator", value=member.discriminator, inline=True)
        embed.add_field(name="Nickname", value=member.nick or "None", inline=True)
        embed.add_field(name="Created At", value=member.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=True)
        embed.add_field(name="Joined At", value=member.joined_at.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=True)
        embed.add_field(name="Roles", value=", ".join([role.name for role in member.roles[1:]]) or "None", inline=False)
        embed.add_field(name="Status", value=str(member.status).title(), inline=True)
        embed.add_field(name="Activity", value=member.activity.name if member.activity else "None", inline=True)
        embed.add_field(name="Bot", value="Yes" if member.bot else "No", inline=True)
        await ctx.send(embed=embed)
    except Exception as e:
        logging.error(f"Error in userinfo command: {str(e)}")
        await ctx.send("An error occurred while fetching user info.")

@bot.command()
async def serverinfo(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        guild = ctx.guild
        embed = discord.Embed(title=f"Server Info - {guild.name}", color=discord.Color.blue())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="ID", value=guild.id, inline=True)
        embed.add_field(name="Owner", value=guild.owner.mention, inline=True)
        embed.add_field(name="Created At", value=guild.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=True)
        embed.add_field(name="Members", value=guild.member_count, inline=True)
        embed.add_field(name="Channels", value=len(guild.channels), inline=True)
        embed.add_field(name="Roles", value=len(guild.roles) - 1, inline=True)
        embed.add_field(name="Verification Level", value=str(guild.verification_level).title(), inline=True)
        embed.add_field(name="Boost Level", value=guild.premium_tier, inline=True)
        await ctx.send(embed=embed)
    except Exception as e:
        logging.error(f"Error in serverinfo command: {str(e)}")
        await ctx.send("An error occurred while fetching server info.")

@bot.command()
@is_superuser_admin_or_mod()
async def clearwarnings(ctx, member: discord.Member):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if ctx.guild.id in warnings and member.id in warnings[ctx.guild.id]:
            del warnings[ctx.guild.id][member.id]
            await ctx.send(f"Warnings cleared for {member.mention}.")
        else:
            await ctx.send(f"{member.mention} has no warnings.")
    except Exception as e:
        logging.error(f"Error in clearwarnings command: {str(e)}")
        await ctx.send("An error occurred while clearing warnings.")

@bot.command()
@is_superuser_admin_or_mod()
async def warn(ctx, member: discord.Member, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if member.id == SUPERUSER_ID:
            await ctx.send("This user is immune to warnings!")
            return
        if member == ctx.author or member == ctx.guild.me:
            await ctx.send("You can't warn yourself or the bot!")
            return
        guild_id = ctx.guild.id
        if guild_id not in warnings:
            warnings[guild_id] = {}
        if member.id not in warnings[guild_id]:
            warnings[guild_id][member.id] = []
        warnings[guild_id][member.id].append({"reason": reason or "None", "timestamp": datetime.datetime.now(datetime.timezone.utc)})
        notified = await notify_user(member, "warned", reason)
        await ctx.send(f"{member.mention} has been warned{' and DM\'d' if notified else ''}. Reason: {reason or 'None'}")
    except Exception as e:
        logging.error(f"Error in warn command: {str(e)}")
        await ctx.send("An error occurred while warning the user.")

@bot.command()
async def warns(ctx, member: discord.Member = None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        member = member or ctx.author
        guild_id = ctx.guild.id
        if guild_id in warnings and member.id in warnings[guild_id]:
            embed = discord.Embed(title=f"Warnings for {member}", color=discord.Color.red())
            for i, warning in enumerate(warnings[guild_id][member.id], 1):
                embed.add_field(name=f"Warning {i}", value=f"Reason: {warning['reason']}\nTime: {warning['timestamp'].strftime('%Y-%m-%d %H:%M:%S UTC')}", inline=False)
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"{member.mention} has no warnings.")
    except Exception as e:
        logging.error(f"Error in warns command: {str(e)}")
        await ctx.send("An error occurred while fetching warnings.")

@bot.command()
@is_superuser_or_admin()
async def role(ctx, action: str, member: discord.Member, role: discord.Role):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if action.lower() not in ["add", "remove"]:
            await ctx.send("Action must be 'add' or 'remove'.")
            return
        if member.id == SUPERUSER_ID and action.lower() == "remove":
            await ctx.send("This user is immune to role removal!")
            return
        if role >= ctx.guild.me.top_role:
            await ctx.send("I can't manage a role higher than or equal to my own!")
            return
        if action.lower() == "add":
            await member.add_roles(role)
            await ctx.send(f"Added {role.name} to {member.mention}.")
        else:
            await member.remove_roles(role)
            await ctx.send(f"Removed {role.name} from {member.mention}.")
    except Exception as e:
        logging.error(f"Error in role command: {str(e)}")
        await ctx.send("An error occurred while managing the role.")

@bot.command()
async def poll(ctx, question: str, *options: str):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if not options or len(options) > 10:
            await ctx.send("Please provide 1-10 options for the poll.")
            return
        embed = discord.Embed(title="Poll", description=question, color=discord.Color.blue())
        for i, option in enumerate(options, 1):
            embed.add_field(name=f"Option {i}", value=option, inline=False)
        message = await ctx.send(embed=embed)
        for i in range(len(options)):
            await message.add_reaction(f"{i+1}\u20e3")
    except Exception as e:
        logging.error(f"Error in poll command: {str(e)}")
        await ctx.send("An error occurred while creating the poll.")

@bot.command()
async def avatar(ctx, member: discord.Member = None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        member = member or ctx.author
        embed = discord.Embed(title=f"{member}'s Avatar", color=discord.Color.blue())
        embed.set_image(url=member.avatar.url if member.avatar else member.default_avatar.url)
        await ctx.send(embed=embed)
    except Exception as e:
        logging.error(f"Error in avatar command: {str(e)}")
        await ctx.send("An error occurred while fetching the avatar.")

@bot.command()
@is_superuser_or_admin()
async def slowmode(ctx, seconds: int):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if seconds < 0 or seconds > 21600:
            await ctx.send("Slowmode must be between 0 and 21600 seconds.")
            return
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send(f"Slowmode set to {seconds} seconds.")
    except Exception as e:
        logging.error(f"Error in slowmode command: {str(e)}")
        await ctx.send("An error occurred while setting slowmode.")

@bot.command()
async def invite(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        invite = await ctx.channel.create_invite(max_age=86400, max_uses=0, temporary=False)
        await ctx.send(f"Invite link: {invite.url}")
    except Exception as e:
        logging.error(f"Error in invite command: {str(e)}")
        await ctx.send("An error occurred while creating the invite.")

@bot.command()
async def botinvite(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        await ctx.send("https://discord.com/oauth2/authorize?client_id=1401297926143086774&permissions=8&integration_type=0&scope=bot+applications.commands")
    except Exception as e:
        logging.error(f"Error in botinvite command: {str(e)}")
        await ctx.send("An error occurred while sending the bot invite link.")

@bot.command()
async def afk(ctx, *, reason="AFK"):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        afk_users[ctx.author.id] = reason
        await ctx.send(f"{ctx.author.mention} is now AFK: {reason}")
    except Exception as e:
        logging.error(f"Error in afk command: {str(e)}")
        await ctx.send("An error occurred while setting AFK status.")

@bot.command()
async def afkstop(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if ctx.author.id in afk_users:
            del afk_users[ctx.author.id]
            await ctx.send(f"{ctx.author.mention} is no longer AFK.")
        else:
            await ctx.send("You are not AFK.")
    except Exception as e:
        logging.error(f"Error in afkstop command: {str(e)}")
        await ctx.send("An error occurred while removing AFK status.")

@bot.command()
async def generateimage(ctx, *, prompt):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        image_data = await generate_image(prompt)
        if image_data:
            await ctx.send(file=discord.File(image_data, "generated_image.png"))
        else:
            await ctx.send("Failed to generate image.")
    except Exception as e:
        logging.error(f"Error in generateimage command: {str(e)}")
        await ctx.send("An error occurred while generating the image.")

@bot.command()
@is_superuser_or_admin()
async def nickname(ctx, member: discord.Member, *, nick: str = None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if member.id == SUPERUSER_ID:
            await ctx.send("This user is immune to nickname changes!")
            return
        await member.edit(nick=nick)
        await ctx.send(f"Nickname for {member.mention} set to {nick or 'default'}.")
    except Exception as e:
        logging.error(f"Error in nickname command: {str(e)}")
        await ctx.send("An error occurred while setting the nickname.")

@bot.command()
async def roleinfo(ctx, role: discord.Role):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        embed = discord.Embed(title=f"Role Info - {role.name}", color=role.color)
        embed.add_field(name="ID", value=role.id, inline=True)
        embed.add_field(name="Created At", value=role.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=True)
        embed.add_field(name="Color", value=str(role.color), inline=True)
        embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No", inline=True)
        embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
        embed.add_field(name="Members", value=len(role.members), inline=True)
        await ctx.send(embed=embed)
    except Exception as e:
        logging.error(f"Error in roleinfo command: {str(e)}")
        await ctx.send("An error occurred while fetching role info.")

@bot.command()
@is_superuser_or_admin()
async def pin(ctx, *, content: str):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if not content:
            await ctx.send("Please provide a message to pin.")
            return
        for pattern in blocked_mentions:
            if re.search(pattern, content, re.IGNORECASE):
                await ctx.send(f"{ctx.author.mention}, pinned message cannot contain @everyone or @here!")
                return
        if ctx.channel.id in pinned_messages:
            last_message_id = pinned_messages[ctx.channel.id].get('last_message_id')
            if last_message_id:
                try:
                    last_message = await ctx.channel.fetch_message(last_message_id)
                    await last_message.delete()
                except discord.NotFound:
                    logging.warning(f"Pinned message {last_message_id} not found, skipping deletion")
                except Exception as e:
                    logging.error(f"Error deleting pinned message: {str(e)}")
        pinned_messages[ctx.channel.id] = {'content': content, 'last_message_id': None}
        new_message = await ctx.channel.send(content)
        pinned_messages[ctx.channel.id]['last_message_id'] = new_message.id
        await ctx.send(f"Pinned message set to: {content}")
    except Exception as e:
        logging.error(f"Error in pin command: {str(e)}")
        await ctx.send("An error occurred while setting the pinned message.")

@bot.command()
async def unpin(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if ctx.channel.id in pinned_messages:
            last_message_id = pinned_messages[ctx.channel.id].get('last_message_id')
            if last_message_id:
                try:
                    last_message = await ctx.channel.fetch_message(last_message_id)
                    await last_message.delete()
                except discord.NotFound:
                    logging.warning(f"Pinned message {last_message_id} not found, skipping deletion")
                except Exception as e:
                    logging.error(f"Error deleting pinned message: {str(e)}")
            del pinned_messages[ctx.channel.id]
            await ctx.send("Pinned message removed.")
        else:
            await ctx.send("No message is pinned in this channel.")
    except Exception as e:
        logging.error(f"Error in unpin command: {str(e)}")
        await ctx.send("An error occurred while removing the pinned message.")

@bot.command()
async def pinstop(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if ctx.channel.id in pinned_messages:
            last_message_id = pinned_messages[ctx.channel.id].get('last_message_id')
            if last_message_id:
                try:
                    last_message = await ctx.channel.fetch_message(last_message_id)
                    await last_message.delete()
                except discord.NotFound:
                    logging.warning(f"Pinned message {last_message_id} not found, skipping deletion")
                except Exception as e:
                    logging.error(f"Error deleting pinned message: {str(e)}")
            del pinned_messages[ctx.channel.id]
            await ctx.send("Pinned message stopped.")
        else:
            await ctx.send("No message is pinned in this channel.")
    except Exception as e:
        logging.error(f"Error in pinstop command: {str(e)}")
        await ctx.send("An error occurred while stopping the pinned message.")

@bot.command()
@is_superuser_or_admin()
async def say(ctx, *, message):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        await ctx.send(message)
        await ctx.message.delete()
    except Exception as e:
        logging.error(f"Error in say command: {str(e)}")
        await ctx.send("An error occurred while sending the message.")

@bot.command()
@is_superuser_or_admin()
async def embed(ctx, *, message):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        embed = discord.Embed(description=message, color=discord.Color.blue())
        await ctx.send(embed=embed)
        await ctx.message.delete()
    except Exception as e:
        logging.error(f"Error in embed command: {str(e)}")
        await ctx.send("An error occurred while sending the embedded message.")

@bot.command()
@is_superuser_or_admin()
async def reactionrole(ctx, message_id: int, role: discord.Role, emoji):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    try:
        if role >= ctx.guild.me.top_role:
            await ctx.send("I can't manage a role higher than or equal to my own!")
            return
        message = await ctx.channel.fetch_message(message_id)
        await message.add_reaction(emoji)
        async def on_reaction_add(reaction, user):
            if user.bot or reaction.message.id != message_id:
                return
            if str(reaction.emoji) == emoji:
                await user.add_roles(role)
        async def on_reaction_remove(reaction, user):
            if user.bot or reaction.message.id != message_id:
                return
            if str(reaction.emoji) == emoji:
                await user.remove_roles(role)
        bot.add_listener(on_reaction_add, 'on_reaction_add')
        bot.add_listener(on_reaction_remove, 'on_reaction_remove')
        await ctx.send(f"Reaction role set: {emoji} for {role.name} on message {message_id}.")
    except discord.NotFound:
        await ctx.send("Message not found or invalid emoji.")
    except Exception as e:
        logging.error(f"Error in reactionrole command: {str(e)}")
        await ctx.send("An error occurred while setting the reaction role.")

@bot.tree.command(name="welcome_messages", description="Sets a welcome message for new members in this channel (Admin only)")
async def welcome_messages(interaction: discord.Interaction, message: str):
    try:
        if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        if interaction.channel.id in disabled_channels:
            await interaction.response.send_message("This channel is disabled for bot commands.", ephemeral=True)
            return
        welcome_channels[interaction.channel.id] = message
        await interaction.response.send_message(f"Welcome message set for this channel: {message}", ephemeral=False)
    except Exception as e:
        logging.error(f"Error in welcome_messages: {str(e)}")
        await interaction.response.send_message("An error occurred while setting the welcome message.", ephemeral=True)

@bot.tree.command(name="welcome_messages_stop", description="Stops welcome messages in this channel (Admin only)")
async def welcome_messages_stop(interaction: discord.Interaction):
    try:
        if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        if interaction.channel.id in disabled_channels:
            await interaction.response.send_message("This channel is disabled for bot commands.", ephemeral=True)
            return
        if interaction.channel.id in welcome_channels:
            del welcome_channels[interaction.channel.id]
            await interaction.response.send_message("Welcome messages stopped in this channel.", ephemeral=False)
        else:
            await interaction.response.send_message("No welcome message is set in this channel.", ephemeral=False)
    except Exception as e:
        logging.error(f"Error in welcome_messages_stop: {str(e)}")
        await interaction.response.send_message("An error occurred while stopping the welcome message.", ephemeral=True)

@bot.tree.command(name="enable_security_channel", description="Enables invite link security in this channel (Admin only)")
async def enable_security_channel(interaction: discord.Interaction):
    try:
        if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        if interaction.channel.id in disabled_channels:
            await interaction.response.send_message("This channel is disabled for bot commands.", ephemeral=True)
            return
        if interaction.channel.id not in security_channels:
            security_channels.add(interaction.channel.id)
            await interaction.response.send_message("Invite link security enabled in this channel. Users posting invite links will be timed out for 1 minute.", ephemeral=False)
        else:
            await interaction.response.send_message("Invite link security is already enabled in this channel!", ephemeral=False)
    except Exception as e:
        logging.error(f"Error in enable_security_channel: {str(e)}")
        await interaction.response.send_message("An error occurred while enabling security in this channel.", ephemeral=True)

@bot.tree.command(name="disable_security_channel", description="Disables invite link security in this channel (Admin only)")
async def disable_security_channel(interaction: discord.Interaction):
    try:
        if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        if interaction.channel.id in disabled_channels:
            await interaction.response.send_message("This channel is disabled for bot commands.", ephemeral=True)
            return
        if interaction.channel.id in security_channels:
            security_channels.remove(interaction.channel.id)
            await interaction.response.send_message("Invite link security disabled in this channel.", ephemeral=False)
        else:
            await interaction.response.send_message("Invite link security is not enabled in this channel.", ephemeral=False)
    except Exception as e:
        logging.error(f"Error in disable_security_channel: {str(e)}")
        await interaction.response.send_message("An error occurred while disabling security in this channel.", ephemeral=True)

@bot.tree.command(name="enable_security_server", description="Enables invite link security in all channels of the server (Admin only)")
async def enable_security_server(interaction: discord.Interaction):
    try:
        if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        if interaction.guild.id not in security_servers:
            security_servers.add(interaction.guild.id)
            await interaction.response.send_message("Invite link security enabled for the entire server. Users posting invite links will be timed out for 1 minute.", ephemeral=False)
        else:
            await interaction.response.send_message("Invite link security is already enabled for the server!", ephemeral=False)
    except Exception as e:
        logging.error(f"Error in enable_security_server: {str(e)}")
        await interaction.response.send_message("An error occurred while enabling server-wide security.", ephemeral=True)

@bot.tree.command(name="disable_security_server", description="Disables invite link security in all channels of the server (Admin only)")
async def disable_security_server(interaction: discord.Interaction):
    try:
        if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        if interaction.guild.id in security_servers:
            security_servers.remove(interaction.guild.id)
            await interaction.response.send_message("Invite link security disabled for the entire server.", ephemeral=False)
        else:
            await interaction.response.send_message("Invite link security is not enabled for the server.", ephemeral=False)
    except Exception as e:
        logging.error(f"Error in disable_security_server: {str(e)}")
        await interaction.response.send_message("An error occurred while disabling server-wide security.", ephemeral=True)

@bot.tree.command(name="command", description="List all available commands")
async def list_commands(interaction: discord.Interaction):
    try:
        embeds = []
        embed1 = discord.Embed(title="PeteZahBot Commands (1/2)", color=discord.Color.blue())
        embed1.add_field(name="p!initiate", value="Activates AI chat in the channel (Admin only).", inline=False)
        embed1.add_field(name="p!stop", value="Disables AI chat in the channel (Admin only).", inline=False)
        embed1.add_field(name="p!ban @user [duration] [reason]", value="Bans a user, optional duration (e.g., 5d, 10m, 2h, 30s) (Admin only).", inline=False)
        embed1.add_field(name="p!unban user_id [reason]", value="Unbans a user by ID (Admin only).", inline=False)
        embed1.add_field(name="p!kick @user [reason]", value="Kicks a user (Admin/Mod).", inline=False)
        embed1.add_field(name="p!mute @user [duration] [reason]", value="Mutes a user, optional duration (e.g., 5d, 10m, 2h, 30s) (Admin/Mod).", inline=False)
        embed1.add_field(name="p!unmute @user [reason]", value="Unmutes a user (Admin only).", inline=False)
        embed1.add_field(name="p!purge amount", value="Deletes up to 100 messages (Admin/Mod).", inline=False)
        embed1.add_field(name="p!lock [reason]", value="Locks the channel, only superuser can send messages (Admin only).", inline=False)
        embed1.add_field(name="p!unlock [reason]", value="Unlocks the channel (Admin only).", inline=False)
        embed1.add_field(name="p!petezah", value="Creates and assigns PeteZah role with admin perms (Superuser only).", inline=False)
        embed1.add_field(name="p!ping", value="Shows bot latency.", inline=False)
        embed1.add_field(name="p!userinfo [@user]", value="Shows user info (defaults to self).", inline=False)
        embed1.add_field(name="p!serverinfo", value="Shows server info.", inline=False)
        embed1.add_field(name="p!clearwarnings @user", value="Clears warnings for a user (Admin/Mod).", inline=False)
        embed1.add_field(name="p!warn @user [reason]", value="Warns a user (Admin/Mod).", inline=False)
        embeds.append(embed1)

        embed2 = discord.Embed(title="PeteZahBot Commands (2/2)", color=discord.Color.blue())
        embed2.add_field(name="p!warns [@user]", value="Shows warnings for a user (defaults to self).", inline=False)
        embed2.add_field(name="p!role add/remove @user @role", value="Adds or removes a role (Admin only).", inline=False)
        embed2.add_field(name="p!poll question option1 option2...", value="Creates a poll with up to 10 options.", inline=False)
        embed2.add_field(name="p!avatar [@user]", value="Shows user avatar (defaults to self).", inline=False)
        embed2.add_field(name="p!slowmode seconds", value="Sets channel slowmode (Admin only).", inline=False)
        embed2.add_field(name="p!invite", value="Creates a server invite link.", inline=False)
        embed2.add_field(name="p!botinvite", value="Provides the bot's invite link.", inline=False)
        embed2.add_field(name="p!afk [reason]", value="Sets AFK status with optional reason.", inline=False)
        embed2.add_field(name="p!afkstop", value="Removes AFK status.", inline=False)
        embed2.add_field(name="p!generateimage prompt", value="Generates an image from a prompt.", inline=False)
        embed2.add_field(name="p!nickname @user [nick]", value="Sets or clears a user's nickname (Admin only).", inline=False)
        embed2.add_field(name="p!roleinfo @role", value="Shows role info.", inline=False)
        embed2.add_field(name="p!pin message", value="Sets a message to be posted after every message in the channel (Admin only).", inline=False)
        embed2.add_field(name="p!unpin", value="Removes the pinned message from the channel.", inline=False)
        embed2.add_field(name="p!pinstop", value="Stops the pinned message from being posted.", inline=False)
        embed2.add_field(name="p!say message", value="Sends a message as the bot (Admin only).", inline=False)
        embed2.add_field(name="p!embed message", value="Sends an embedded message (Admin only).", inline=False)
        embed2.add_field(name="p!reactionrole message_id @role emoji", value="Sets a reaction role (Admin only).", inline=False)
        embed2.add_field(name="/command", value="Shows this command list.", inline=False)
        embed2.add_field(name="/welcome_messages message", value="Sets a welcome message for new members in the channel (Admin only).", inline=False)
        embed2.add_field(name="/welcome_messages_stop", value="Stops welcome messages in the channel (Admin only).", inline=False)
        embed2.add_field(name="/enable_security_channel", value="Enables invite link security in the channel (Admin only).", inline=False)
        embed2.add_field(name="/disable_security_channel", value="Disables invite link security in the channel (Admin only).", inline=False)
        embed2.add_field(name="/enable_security_server", value="Enables invite link security in all channels (Admin only).", inline=False)
        embed2.add_field(name="/disable_security_server", value="Disables invite link security in all channels (Admin only).", inline=False)
        embed2.add_field(name="/stopchannel", value="Completely disables the bot in this channel (Admin only).", inline=False)
        embed2.add_field(name="/reenablechannel", value="Re-enables the bot in this channel (Admin only).", inline=False)
        embeds.append(embed2)

        await interaction.response.send_message(embeds=embeds, ephemeral=False)
    except Exception as e:
        logging.error(f"Error in list_commands: {str(e)}")
        await interaction.response.send_message("An error occurred while listing commands.", ephemeral=True)

@bot.tree.command(name="enable_mod_perms", description="Grants moderator permissions to a specified role or member (Admin only)")
async def enable_mod_perms(interaction: discord.Interaction, role: discord.Role = None, member: discord.Member = None):
    try:
        if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        if not role and not member:
            await interaction.response.send_message("Please specify a role or a member!", ephemeral=True)
            return
        if role and role >= interaction.guild.me.top_role:
            await interaction.response.send_message("I can't manage a role higher than or equal to my own!", ephemeral=True)
            return
        permissions = discord.Permissions(
            manage_messages=True,
            kick_members=True,
            moderate_members=True
        )
        if role:
            await role.edit(permissions=permissions, reason="Enabled moderator permissions for role")
            await interaction.response.send_message(f"Moderator permissions granted to {role.mention}. Members with this role can now use p!kick, p!mute, p!purge, and p!warn.", ephemeral=False)
        if member:
            mod_role = discord.utils.get(interaction.guild.roles, name="Moderator")
            if not mod_role:
                mod_role = await interaction.guild.create_role(
                    name="Moderator",
                    permissions=permissions,
                    reason="Created Moderator role for member"
                )
            await member.add_roles(mod_role, reason="Enabled moderator permissions for member")
            await interaction.response.send_message(f"Moderator permissions granted to {member.mention} via Moderator role.", ephemeral=False)
    except Exception as e:
        logging.error(f"Error in enable_mod_perms: {str(e)}")
        await interaction.response.send_message("An error occurred while granting moderator permissions.", ephemeral=True)

@bot.tree.command(name="enable_admin_perms", description="Grants administrator permissions to a specified role or member (Admin only)")
async def enable_admin_perms(interaction: discord.Interaction, role: discord.Role = None, member: discord.Member = None):
    try:
        if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        if not role and not member:
            await interaction.response.send_message("Please specify a role or a member!", ephemeral=True)
            return
        if role and role >= interaction.guild.me.top_role:
            await interaction.response.send_message("I can't manage a role higher than or equal to my own!", ephemeral=True)
            return
        permissions = discord.Permissions(administrator=True)
        if role:
            await role.edit(permissions=permissions, reason="Enabled administrator permissions for role")
            await interaction.response.send_message(f"Administrator permissions granted to {role.mention}. Members with this role can now use all commands.", ephemeral=False)
        if member:
            admin_role = discord.utils.get(interaction.guild.roles, name="Administrator")
            if not admin_role:
                admin_role = await interaction.guild.create_role(
                    name="Administrator",
                    permissions=permissions,
                    reason="Created Administrator role for member"
                )
            await member.add_roles(admin_role, reason="Enabled administrator permissions for member")
            await interaction.response.send_message(f"Administrator permissions granted to {member.mention} via Administrator role.", ephemeral=False)
    except Exception as e:
        logging.error(f"Error in enable_admin_perms: {str(e)}")
        await interaction.response.send_message("An error occurred while granting administrator permissions.", ephemeral=True)

@bot.tree.command(name="stopchannel", description="Completely disables the bot in this channel (Admin only)")
async def stopchannel(interaction: discord.Interaction):
    try:
        if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        if interaction.channel.id not in disabled_channels:
            disabled_channels.add(interaction.channel.id)
            if interaction.channel.id in active_channels:
                active_channels.remove(interaction.channel.id)
            if interaction.channel.id in message_history:
                del message_history[interaction.channel.id]
            if interaction.channel.id in pinned_messages:
                last_message_id = pinned_messages[interaction.channel.id].get('last_message_id')
                if last_message_id:
                    try:
                        last_message = await interaction.channel.fetch_message(last_message_id)
                        await last_message.delete()
                    except discord.NotFound:
                        logging.warning(f"Pinned message {last_message_id} not found, skipping deletion")
                    except Exception as e:
                        logging.error(f"Error deleting pinned message: {str(e)}")
                del pinned_messages[interaction.channel.id]
            if interaction.channel.id in welcome_channels:
                del welcome_channels[interaction.channel.id]
            if interaction.channel.id in security_channels:
                security_channels.remove(interaction.channel.id)
            await interaction.response.send_message("PeteZahBot is now completely disabled in this channel!", ephemeral=False)
        else:
            await interaction.response.send_message("PeteZahBot is already disabled in this channel!", ephemeral=False)
    except Exception as e:
        logging.error(f"Error in stopchannel: {str(e)}")
        await interaction.response.send_message("An error occurred while disabling the bot in this channel.", ephemeral=True)

@bot.tree.command(name="reenablechannel", description="Re-enables the bot in this channel (Admin only)")
async def reenablechannel(interaction: discord.Interaction):
    try:
        if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
            return
        if interaction.channel.id in disabled_channels:
            disabled_channels.remove(interaction.channel.id)
            await interaction.response.send_message("PeteZahBot is now re-enabled in this channel!", ephemeral=False)
        else:
            await interaction.response.send_message("PeteZahBot is already enabled in this channel!", ephemeral=False)
    except Exception as e:
        logging.error(f"Error in reenablechannel: {str(e)}")
        await interaction.response.send_message("An error occurred while re-enabling the bot in this channel.", ephemeral=True)

@bot.event
async def on_command_error(ctx, error):
    if ctx.channel.id in disabled_channels:
        return
    try:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("You need administrator permissions to use this command!")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Missing required argument. Check command usage.")
        elif isinstance(error, commands.MemberNotFound):
            await ctx.send("Member not found. Please mention a valid member.")
        elif isinstance(error, commands.MessageNotFound):
            await ctx.send("Message not found. Please provide a valid message link or ID.")
        else:
            logging.error(f"Command error: {str(error)}")
            await ctx.send(f"An error occurred: {str(error)}")
    except Exception as e:
        logging.error(f"Error in on_command_error: {str(e)}")

bot.run(os.getenv('DISCORD_TOKEN'))
