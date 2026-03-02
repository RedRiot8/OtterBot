import os
import json
import asyncio
import random

import discord
from pathlib import Path
from dotenv import load_dotenv
from discord.ext import commands

load_dotenv()

# Intents (message content is needed to read answers)
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

BASE_DIR = Path(__file__).resolve().parent
SCORES_FILE = BASE_DIR / "scores.json"
QUESTIONS_FILE = BASE_DIR / "questions.json"


def load_scores() -> dict:
    if not SCORES_FILE.exists():
        return {}
    try:
        with SCORES_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_scores(scores: dict) -> None:
    try:
        with SCORES_FILE.open("w", encoding="utf-8") as f:
            json.dump(scores, f, indent=2)
    except OSError:
        # In a simple bot we silently ignore write errors
        pass


def load_questions() -> list:
    if not QUESTIONS_FILE.exists():
        return []
    try:
        with QUESTIONS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except (json.JSONDecodeError, OSError):
        return []


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")


@bot.command(name="startotter")
async def startotter(ctx: commands.Context):
    """Starts a 3-question trivia scavenger hunt."""
    await ctx.send(
        f"Welcome to the scavenger hunt, {ctx.author.mention}! "
        "Answer the questions correctly to proceed. Let's begin!"
    )

    def check(m: discord.Message):
        # Only accept messages from the command user in the same channel
        return m.author == ctx.author and m.channel == ctx.channel
    questions = load_questions()
    if not questions:
        await ctx.send("No questions are configured yet.")
        return

    # Choose three unique random questions (or fewer if not enough exist)
    num_to_ask = min(3, len(questions))
    selected_questions = random.sample(questions, k=num_to_ask)

    correct_count = 0

    for idx, item in enumerate(selected_questions, start=1):
        question = item.get("question", "No question text provided.")
        correct_answer = str(item.get("answer", "")).strip().lower()

        await ctx.send(question)

        while True:
            try:
                # Wait for the user's next message as an answer
                msg: discord.Message = await bot.wait_for(
                    "message", timeout=60.0, check=check
                )
            except asyncio.TimeoutError:
                await ctx.send(
                    f"{ctx.author.mention}, time's up! Hunt canceled due to inactivity."
                )
                return

            user_answer = msg.content.strip().lower()

            if user_answer == correct_answer:
                correct_count += 1
                if idx < len(selected_questions):
                    await ctx.send(
                        f"🎉 Correct, {ctx.author.mention}! Here's your next clue..."
                    )
                else:
                    await ctx.send(
                        f"🎉 Correct, {ctx.author.mention}! "
                        "You've completed the scavenger hunt! Great job!"
                    )
                break  # Move on to the next question
            else:
                await ctx.send(
                    f"❌ Not quite, {ctx.author.mention}. Try again!"
                )

    # Award 10 points only if all three questions in this session were answered correctly
    if correct_count == 3:
        scores = load_scores()
        user_id = str(ctx.author.id)
        current = scores.get(user_id, {"name": ctx.author.name, "points": 0})
        current["name"] = ctx.author.name
        current["points"] = int(current.get("points", 0)) + 10
        scores[user_id] = current
        save_scores(scores)


@bot.command(name="topotter")
async def topotter(ctx: commands.Context):
    """Show the top 5 players by points."""
    scores = load_scores()
    if not scores:
        await ctx.send("No scores yet. Play the scavenger hunt with `!startotter`!")
        return

    # Sort by points descending
    sorted_players = sorted(
        scores.items(),
        key=lambda item: int(item[1].get("points", 0)),
        reverse=True,
    )[:5]

    embed = discord.Embed(
        title="Scavenger Hunt Leaderboard",
        color=discord.Color.gold(),
    )

    for rank, (user_id, info) in enumerate(sorted_players, start=1):
        name = info.get("name", f"User {user_id}")
        points = info.get("points", 0)
        embed.add_field(
            name=f"#{rank} {name}",
            value=f"Points: {points}",
            inline=False,
        )

    await ctx.send(embed=embed)


if __name__ == "__main__":
    # Reset scores at each run so every session starts fresh
    save_scores({})
    bot.run(os.getenv("DISCORD_TOKEN"))
