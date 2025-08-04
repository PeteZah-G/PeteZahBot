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

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='p!', intents=intents)

active_channels = set()
blocked_mentions = [r'@everyone', r'@here']
message_history = {}
warnings = {}
afk_users = {}
pinned_messages = {}
SUPERUSER_ID = 1311722282317779097
MOD_ROLE_NAME = "Moderator"

async def generate_ai_response(message):
    print(f"Generating AI response for message: {message.content}")
    channel_id = message.channel.id
    if channel_id not in message_history:
        message_history[channel_id] = deque(maxlen=7)
    message_history[channel_id].append({"role": "user", "content": message.content})
    prompt = "\n".join([f"{msg['role']}: {msg['content']}" for msg in message_history[channel_id]])
    encoded_prompt = urllib.parse.quote(prompt)
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f'https://text.pollinations.ai/{encoded_prompt}'
            ) as response:
                print(f"Pollinations API response status: {response.status}")
                if response.status == 200:
                    response_text = await response.text()
                    print(f"Pollinations API response: {response_text}")
                    for pattern in blocked_mentions:
                        response_text = re.sub(pattern, '[REDACTED]', response_text, flags=re.IGNORECASE)
                    return response_text[:2000] if len(response_text) > 2000 else response_text
                return f"API error: Status {response.status}"
        except Exception as e:
            print(f"AI response error: {str(e)}")
            return "Error connecting to AI service."

async def generate_image(prompt):
    encoded_prompt = urllib.parse.quote(prompt)
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f'https://image.pollinations.ai/prompt/{encoded_prompt}'
            ) as response:
                print(f"Pollinations Image API response status: {response.status}")
                if response.status == 200:
                    image_data = await response.read()
                    return io.BytesIO(image_data)
                return None
        except Exception as e:
            print(f"Image generation error: {str(e)}")
            return None

async def notify_user(member, action, reason=None):
    try:
        embed = discord.Embed(title=f"You have been {action}", color=discord.Color.red())
        embed.add_field(name="Server", value=member.guild.name, inline=False)
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"Action taken at {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        await member.send(embed=embed)
        return True
    except:
        return False

def is_superuser_or_admin():
    def predicate(ctx):
        return ctx.author.id == SUPERUSER_ID or ctx.author.guild_permissions.administrator
    return commands.check(predicate)

def is_superuser_admin_or_mod():
    def predicate(ctx):
        mod_role = discord.utils.get(ctx.guild.roles, name=MOD_ROLE_NAME)
        return (ctx.author.id == SUPERUSER_ID or 
                ctx.author.guild_permissions.administrator or 
                (mod_role and mod_role in ctx.author.roles))
    return commands.check(predicate)

@bot.event
async def on_ready():
    print(f'Bot is ready as {bot.user}')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Failed to sync slash commands: {str(e)}")

@bot.event
async def on_message(message):
    if message.author.bot:
        print(f"Ignoring message from bot {message.author} in channel {message.channel.id}")
        await bot.process_commands(message)
        return

    if message.channel.id not in active_channels:
        print(f"Ignoring message from {message.author} in channel {message.channel.id} (not active)")
        if message.channel.id in pinned_messages and not message.content.startswith('p!'):
            last_message_id = pinned_messages[message.channel.id].get('last_message_id')
            if last_message_id:
                try:
                    last_message = await message.channel.fetch_message(last_message_id)
                    await last_message.delete()
                except:
                    pass
            new_message = await message.channel.send(pinned_messages[message.channel.id]['content'])
            pinned_messages[message.channel.id]['last_message_id'] = new_message.id
        await bot.process_commands(message)
        return

    print(f"Processing message: {message.content} in channel {message.channel.id}")
    for pattern in blocked_mentions:
        if re.search(pattern, message.content, re.IGNORECASE):
            await message.delete()
            await message.channel.send(f"{message.author.mention}, please don't use mass mentions!", delete_after=5)
            return

    await asyncio.sleep(1)
    ai_response = await generate_ai_response(message)
    message_history[message.channel.id].append({"role": "assistant", "content": ai_response})
    await message.channel.send(ai_response)

    if message.channel.id in pinned_messages and not message.content.startswith('p!'):
        last_message_id = pinned_messages[message.channel.id].get('last_message_id')
        if last_message_id:
            try:
                last_message = await message.channel.fetch_message(last_message_id)
                await last_message.delete()
            except:
                pass
        new_message = await message.channel.send(pinned_messages[message.channel.id]['content'])
        pinned_messages[message.channel.id]['last_message_id'] = new_message.id

    await bot.process_commands(message)

@bot.command()
@is_superuser_or_admin()
async def initiate(ctx):
    print(f"Received p!initiate in channel {ctx.channel.id} by {ctx.author}")
    if ctx.channel.id not in active_channels:
        active_channels.add(ctx.channel.id)
        await ctx.send("PeteZahBot AI is now active in this channel!")
    else:
        await ctx.send("PeteZahBot AI is already active here!")

@bot.command()
@is_superuser_or_admin()
async def stop(ctx):
    if ctx.channel.id in active_channels:
        active_channels.remove(ctx.channel.id)
        if ctx.channel.id in message_history:
            del message_history[ctx.channel.id]
        await ctx.send("PeteZahBot AI is now disabled in this channel!")
    else:
        await ctx.send("PeteZahBot AI is not active in this channel!")

@bot.command()
@is_superuser_or_admin()
async def ban(ctx, member: discord.Member, *, reason=None):
    if member.id == SUPERUSER_ID:
        await ctx.send("This user is immune to bans!")
        return
    if member == ctx.author or member == ctx.guild.me:
        await ctx.send("You can't ban yourself or the bot!")
        return
    notified = await notify_user(member, "banned", reason)
    await member.ban(reason=reason)
    await ctx.send(f"{member.mention} has been banned{' and DM\'d' if notified else ''}. Reason: {reason or 'None'}")

@bot.command()
@is_superuser_or_admin()
async def unban(ctx, user_id: int, *, reason=None):
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=reason)
        await ctx.send(f"{user.name}#{user.discriminator} has been unbanned. Reason: {reason or 'None'}")
    except:
        await ctx.send("User not found or not banned.")

@bot.command()
@is_superuser_admin_or_mod()
async def kick(ctx, member: discord.Member, *, reason=None):
    if member.id == SUPERUSER_ID:
        await ctx.send("This user is immune to kicks!")
        return
    if member == ctx.author or member == ctx.guild.me:
        await ctx.send("You can't kick yourself or the bot!")
        return
    notified = await notify_user(member, "kicked", reason)
    await member.kick(reason=reason)
    await ctx.send(f"{member.mention} has been kicked{' and DM\'d' if notified else ''}. Reason: {reason or 'None'}")

@bot.command()
@is_superuser_admin_or_mod()
async def mute(ctx, member: discord.Member, *, reason=None):
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
    notified = await notify_user(member, "muted", reason)
    await member.add_roles(mute_role, reason=reason)
    await ctx.send(f"{member.mention} has been muted{' and DM\'d' if notified else ''}. Reason: {reason or 'None'}")

@bot.command()
@is_superuser_or_admin()
async def unmute(ctx, member: discord.Member, *, reason=None):
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

@bot.command()
@is_superuser_admin_or_mod()
async def purge(ctx, amount: int):
    if amount < 1 or amount > 100:
        await ctx.send("Please specify a number between 1 and 100.")
        return
    await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"Purged {amount} messages.", delete_after=5)

@bot.command()
@is_superuser_or_admin()
async def lock(ctx, *, reason=None):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send(f"Channel locked. Reason: {reason or 'None'}")

@bot.command()
@is_superuser_or_admin()
async def unlock(ctx, *, reason=None):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = None
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send(f"Channel unlocked. Reason: {reason or 'None'}")

@bot.command()
async def ping(ctx):
    latency = round(bot.latency * 1000)
    await ctx.send(f"Pong! Latency: {latency}ms")

@bot.command()
async def userinfo(ctx, member: discord.Member = None):
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

@bot.command()
async def serverinfo(ctx):
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

@bot.command()
@is_superuser_admin_or_mod()
async def clearwarnings(ctx, member: discord.Member):
    if ctx.guild.id in warnings and member.id in warnings[ctx.guild.id]:
        del warnings[ctx.guild.id][member.id]
        await ctx.send(f"Warnings cleared for {member.mention}.")
    else:
        await ctx.send(f"{member.mention} has no warnings.")

@bot.command()
@is_superuser_admin_or_mod()
async def warn(ctx, member: discord.Member, *, reason=None):
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

@bot.command()
async def warns(ctx, member: discord.Member = None):
    member = member or ctx.author
    guild_id = ctx.guild.id
    if guild_id in warnings and member.id in warnings[guild_id]:
        embed = discord.Embed(title=f"Warnings for {member}", color=discord.Color.red())
        for i, warning in enumerate(warnings[guild_id][member.id], 1):
            embed.add_field(name=f"Warning {i}", value=f"Reason: {warning['reason']}\nTime: {warning['timestamp'].strftime('%Y-%m-%d %H:%M:%S UTC')}", inline=False)
        await ctx.send(embed=embed)
    else:
        await ctx.send(f"{member.mention} has no warnings.")

@bot.command()
@is_superuser_or_admin()
async def role(ctx, action: str, member: discord.Member, role: discord.Role):
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

@bot.command()
async def poll(ctx, question: str, *options: str):
    if not options or len(options) > 10:
        await ctx.send("Please provide 1-10 options for the poll.")
        return
    embed = discord.Embed(title="Poll", description=question, color=discord.Color.blue())
    for i, option in enumerate(options, 1):
        embed.add_field(name=f"Option {i}", value=option, inline=False)
    message = await ctx.send(embed=embed)
    for i in range(len(options)):
        await message.add_reaction(f"{i+1}\u20e3")

@bot.command()
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title=f"{member}'s Avatar", color=discord.Color.blue())
    embed.set_image(url=member.avatar.url if member.avatar else member.default_avatar.url)
    await ctx.send(embed=embed)

@bot.command()
@is_superuser_or_admin()
async def slowmode(ctx, seconds: int):
    if seconds < 0 or seconds > 21600:
        await ctx.send("Slowmode must be between 0 and 21600 seconds.")
        return
    await ctx.channelmediatimeoutchannel.edit(slowmode_delay=seconds)
    await ctx.send(f"Slowmode set to {seconds} seconds.")

@bot.command()
async def invite(ctx):
    invite = await ctx.channel.create_invite(max_age=86400, max_uses=0, temporary=False)
    await ctx.send(f"Invite link: {invite.url}")

@bot.command()
async def afk(ctx, *, reason="AFK"):
    afk_users[ctx.author.id] = reason
    await ctx.send(f"{ctx.author.mention} is now AFK: {reason}")

@bot.command()
async def afkstop(ctx):
    if ctx.author.id in afk_users:
        del afk_users[ctx.author.id]
        await ctx.send(f"{ctx.author.mention} is no longer AFK.")
    else:
        await ctx.send("You are not AFK.")

@bot.command()
async def generateimage(ctx, *, prompt):
    image_data = await generate_image(prompt)
    if image_data:
        await ctx.send(file=discord.File(image_data, "generated_image.png"))
    else:
        await ctx.send("Failed to generate image.")

@bot.command()
@is_superuser_or_admin()
async def nickname(ctx, member: discord.Member, *, nick: str = None):
    if member.id == SUPERUSER_ID:
        await ctx.send("This user is immune to nickname changes!")
        return
    await member.edit(nick=nick)
    await ctx.send(f"Nickname for {member.mention} set to {nick or 'default'}.")

@bot.command()
async def roleinfo(ctx, role: discord.Role):
    embed = discord.Embed(title=f"Role Info - {role.name}", color=role.color)
    embed.add_field(name="ID", value=role.id, inline=True)
    embed.add_field(name="Created At", value=role.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=True)
    embed.add_field(name="Color", value=str(role.color), inline=True)
    embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No", inline=True)
    embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
    embed.add_field(name="Members", value=len(role.members), inline=True)
    await ctx.send(embed=embed)

@bot.command()
async def pin(ctx, *, content: str):
    if not content:
        await ctx.send("Please provide a message to pin.")
        return
    if ctx.channel.id in pinned_messages:
        last_message_id = pinned_messages[ctx.channel.id].get('last_message_id')
        if last_message_id:
            try:
                last_message = await ctx.channel.fetch_message(last_message_id)
                await last_message.delete()
            except:
                pass
    pinned_messages[ctx.channel.id] = {'content': content, 'last_message_id': None}
    new_message = await ctx.channel.send(content)
    pinned_messages[ctx.channel.id]['last_message_id'] = new_message.id
    await ctx.send(f"Pinned message set to: {content}")

@bot.command()
async def unpin(ctx):
    if ctx.channel.id in pinned_messages:
        last_message_id = pinned_messages[ctx.channel.id].get('last_message_id')
        if last_message_id:
            try:
                last_message = await ctx.channel.fetch_message(last_message_id)
                await last_message.delete()
            except:
                pass
        del pinned_messages[ctx.channel.id]
        await ctx.send("Pinned message removed.")
    else:
        await ctx.send("No message is pinned in this channel.")

@bot.command()
@is_superuser_or_admin()
async def say(ctx, *, message):
    await ctx.send(message)
    await ctx.message.delete()

@bot.command()
@is_superuser_or_admin()
async def embed(ctx, *, message):
    embed = discord.Embed(description=message, color=discord.Color.blue())
    await ctx.send(embed=embed)
    await ctx.message.delete()

@bot.command()
@is_superuser_or_admin()
async def reactionrole(ctx, message_id: int, role: discord.Role, emoji):
    if role >= ctx.guild.me.top_role:
        await ctx.send("I can't manage a role higher than or equal to my own!")
        return
    try:
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
    except:
        await ctx.send("Message not found or invalid emoji.")

@bot.tree.command(name="command", description="List all available commands")
async def list_commands(interaction: discord.Interaction):
    embeds = []
    embed1 = discord.Embed(title="PeteZahBot Commands (1/2)", color=discord.Color.blue())
    embed1.add_field(name="p!initiate", value="Activates AI chat in the channel (Admin only).", inline=False)
    embed1.add_field(name="p!stop", value="Disables AI chat in the channel (Admin only).", inline=False)
    embed1.add_field(name="p!ban @user [reason]", value="Bans a user (Admin only).", inline=False)
    embed1.add_field(name="p!unban user_id [reason]", value="Unbans a user by ID (Admin only).", inline=False)
    embed1.add_field(name="p!kick @user [reason]", value="Kicks a user (Admin/Mod).", inline=False)
    embed1.add_field(name="p!mute @user [reason]", value="Mutes a user (Admin/Mod).", inline=False)
    embed1.add_field(name="p!unmute @user [reason]", value="Unmutes a user (Admin only).", inline=False)
    embed1.add_field(name="p!purge amount", value="Deletes up to 100 messages (Admin/Mod).", inline=False)
    embed1.add_field(name="p!lock [reason]", value="Locks the channel (Admin only).", inline=False)
    embed1.add_field(name="p!unlock [reason]", value="Unlocks the channel (Admin only).", inline=False)
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
    embed2.add_field(name="p!afk [reason]", value="Sets AFK status with optional reason.", inline=False)
    embed2.add_field(name="p!afkstop", value="Removes AFK status.", inline=False)
    embed2.add_field(name="p!generateimage prompt", value="Generates an image from a prompt.", inline=False)
    embed2.add_field(name="p!nickname @user [nick]", value="Sets or clears a user's nickname (Admin only).", inline=False)
    embed2.add_field(name="p!roleinfo @role", value="Shows role info.", inline=False)
    embed2.add_field(name="p!pin message", value="Sets a message to be posted after every message in the channel.", inline=False)
    embed2.add_field(name="p!unpin", value="Removes the pinned message from the channel.", inline=False)
    embed2.add_field(name="p!say message", value="Sends a message as the bot (Admin only).", inline=False)
    embed2.add_field(name="p!embed message", value="Sends an embedded message (Admin only).", inline=False)
    embed2.add_field(name="p!reactionrole message_id @role emoji", value="Sets a reaction role (Admin only).", inline=False)
    embed2.add_field(name="/command", value="Shows this command list.", inline=False)
    embeds.append(embed2)

    await interaction.response.send_message(embeds=embeds, ephemeral=False)

@bot.tree.command(name="enable_mod_perms", description="Grants moderator permissions to a specified role (Admin only)")
async def enable_mod_perms(interaction: discord.Interaction, role: discord.Role):
    if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
        return
    if role >= interaction.guild.me.top_role:
        await interaction.response.send_message("I can't manage a role higher than or equal to my own!", ephemeral=True)
        return
    permissions = discord.Permissions(
        manage_messages=True,
        kick_members=True,
        mute_members=True
    )
    await role.edit(permissions=permissions, reason="Enabled moderator permissions")
    global MOD_ROLE_NAME
    MOD_ROLE_NAME = role.name
    await interaction.response.send_message(f"Moderator permissions granted to {role.mention}. Members with this role can now use p!kick, p!mute, p!purge, and p!warn.", ephemeral=False)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need administrator permissions to use this command!")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing required argument. Check command usage.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Member not found. Please mention a valid member.")
    elif isinstance(error, commands.MessageNotFound):
        await ctx.send("Message not found. Please provide a valid message link or ID.")
    else:
        print(f"Command error: {str(error)}")
        await ctx.send(f"An error occurred: {str(error)}")

bot.run(os.getenv('DISCORD_TOKEN'))
