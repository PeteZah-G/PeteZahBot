import discord
from discord.ext import commands
import os
import aiohttp
import json
import re

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='h!', intents=intents)

active_channels = set()
blocked_mentions = [r'@everyone', r'@here']

async def generate_ai_response(message):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                'https://api.x.ai/v1/chat/completions',
                headers={'Authorization': f'Bearer {os.getenv("XAI_API_KEY")}'},
                json={
                    'model': 'grok',
                    'messages': [{'role': 'user', 'content': message.content}],
                    'max_tokens': 200
                }
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['choices'][0]['message']['content']
                return "Sorry, I couldn't process that. Try again!"
        except:
            return "Error connecting to AI service."

@bot.event
async def on_ready():
    print(f'Bot is ready as {bot.user}')

@bot.event
async def on_message(message):
    if message.author.bot or message.channel.id not in active_channels:
        await bot.process_commands(message)
        return

    for pattern in blocked_mentions:
        if re.search(pattern, message.content, re.IGNORECASE):
            await message.delete()
            await message.channel.send(f"{message.author.mention}, please don't use mass mentions!", delete_after=5)
            return

    ai_response = await generate_ai_response(message)
    await message.channel.send(ai_response)
    await bot.process_commands(message)

@bot.command()
@commands.has_permissions(administrator=True)
async def initiate(ctx):
    if ctx.channel.id not in active_channels:
        active_channels.add(ctx.channel.id)
        await ctx.send("HavenAI is now active in this channel!")
    else:
        await ctx.send("HavenAI is already active here!")

@bot.command()
@commands.has_permissions(administrator=True)
async def stop(ctx):
    if ctx.channel.id in active_channels:
        active_channels.remove(ctx.channel.id)
        await ctx.send("HavenAI is now disabled in this channel!")
    else:
        await ctx.send("HavenAI is not active in this channel!")

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
async def purge(ctx, amount: int):
    if amount < 1 or amount > 100:
        await ctx.send("Please specify a number between 1 and 100.")
        return
    await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"Purged {amount} messages.", delete_after=5)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need administrator permissions to use this command!")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing required argument. Check command usage.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Member not found. Please mention a valid member.")
    else:
        await ctx.send(f"An error occurred: {str(error)}")

bot.run(os.getenv('DISCORD_TOKEN'))
