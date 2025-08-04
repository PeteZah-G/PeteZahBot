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

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='p!', intents=intents)

active_channels = set()
blocked_mentions = [r'@everyone', r'@here']
message_history = {}

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

@bot.event
async def on_ready():
    print(f'Bot is ready as {bot.user}')
    await bot.tree.sync()

@bot.event
async def on_message(message):
    if message.author.bot or message.channel.id not in active_channels:
        print(f"Ignoring message from {message.author} in channel {message.channel.id}")
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
    await bot.process_commands(message)

@bot.command()
@commands.has_permissions(administrator=True)
async def initiate(ctx):
    print(f"Received p!initiate in channel {ctx.channel.id} by {ctx.author}")
    if ctx.channel.id not in active_channels:
        active_channels.add(ctx.channel.id)
        await ctx.send("PeteZahBot AI is now active in this channel!")
    else:
        await ctx.send("PeteZahBot AI is already active here!")

@bot.command()
@commands.has_permissions(administrator=True)
async def stop(ctx):
    if ctx.channel.id in active_channels:
        active_channels.remove(ctx.channel.id)
        if ctx.channel.id in message_history:
            del message_history[ctx.channel.id]
        await ctx.send("PeteZahBot AI is now disabled in this channel!")
    else:
        await ctx.send("PeteZahBot AI is not active in this channel!")

@bot.command()
@commands.has_permissions(administrator=True)
async def ban(ctx, member: discord.Member, *, reason=None):
    if member == ctx.author or member == ctx.guild.me:
        await ctx.send("You can't ban yourself or the bot!")
        return
    await member.ban(reason=reason)
    await ctx.send(f"{member.mention} has been banned. Reason: {reason or 'None'}")

@bot.command()
@commands.has_permissions(administrator=True)
async def unban(ctx, user_id: int, *, reason=None):
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=reason)
        await ctx.send(f"{user.name}#{user.discriminator} has been unbanned. Reason: {reason or 'None'}")
    except:
        await ctx.send("User not found or not banned.")

@bot.command()
@commands.has_permissions(administrator=True)
async def kick(ctx, member: discord.Member, *, reason=None):
    if member == ctx.author or member == ctx.guild.me:
        await ctx.send("You can't kick yourself or the bot!")
        return
    await member.kick(reason=reason)
    await ctx.send(f"{member.mention} has been kicked. Reason: {reason or 'None'}")

@bot.command()
@commands.has_permissions(administrator=True)
async def mute(ctx, member: discord.Member, *, reason=None):
    if member == ctx.author or member == ctx.guild.me:
        await ctx.send("You can't mute yourself or the bot!")
        return
    mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if not mute_role:
        mute_role = await ctx.guild.create_role(name="Muted")
        for channel in ctx.guild.channels:
            await channel.set_permissions(mute_role, send_messages=False)
    await member.add_roles(mute_role, reason=reason)
    await ctx.send(f"{member.mention} has been muted. Reason: {reason or 'None'}")

@bot.command()
@commands.has_permissions(administrator=True)
async def unmute(ctx, member: discord.Member, *, reason=None):
    if member == ctx.author or member == ctx.guild.me:
        await ctx.send("You can't unmute yourself or the bot!")
        return
    mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if mute_role and mute_role in member.roles:
        await member.remove_roles(mute_role, reason=reason)
        await ctx.send(f"{member.mention} has been unmuted. Reason: {reason or 'None'}")
    else:
        await ctx.send(f"{member.mention} is not muted!")

@bot.command()
@commands.has_permissions(administrator=True)
async def purge(ctx, amount: int):
    if amount < 1 or amount > 100:
        await ctx.send("Please specify a number between 1 and 100.")
        return
    await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"Purged {amount} messages.", delete_after=5)

@bot.command()
@commands.has_permissions(administrator=True)
async def lock(ctx, *, reason=None):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send(f"Channel locked. Reason: {reason or 'None'}")

@bot.command()
@commands.has_permissions(administrator=True)
async def unlock(ctx, *, reason=None):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = None
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send(f"Channel unlocked. Reason: {reason or 'None'}")

@bot.tree.command(name="command", description="List all available commands")
async def command(interaction: discord.Interaction):
    embed = discord.Embed(title="PeteZahBot Commands", color=discord.Color.blue())
    embed.add_field(name="p!initiate", value="Activates AI chat in the channel (Admin only).", inline=False)
    embed.add_field(name="p!stop", value="Disables AI chat in the channel (Admin only).", inline=False)
    embed.add_field(name="p!ban @user [reason]", value="Bans a user (Admin only).", inline=False)
    embed.add_field(name="p!unban user_id [reason]", value="Unbans a user by ID (Admin only).", inline=False)
    embed.add_field(name="p!kick @user [reason]", value="Kicks a user (Admin only).", inline=False)
    embed.add_field(name="p!mute @user [reason]", value="Mutes a user (Admin only).", inline=False)
    embed.add_field(name="p!unmute @user [reason]", value="Unmutes a user (Admin only).", inline=False)
    embed.add_field(name="p!purge amount", value="Deletes up to 100 messages (Admin only).", inline=False)
    embed.add_field(name="p!lock [reason]", value="Locks the channel (Admin only).", inline=False)
    embed.add_field(name="p!unlock [reason]", value="Unlocks the channel (Admin only).", inline=False)
    embed.add_field(name="/command", value="Shows this command list.", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need administrator permissions to use this command!")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing required argument. Check command usage.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Member not found. Please mention a valid member.")
    else:
        print(f"Command error: {str(error)}")
        await ctx.send(f"An error occurred: {str(error)}")

bot.run(os.getenv('DISCORD_TOKEN'))
